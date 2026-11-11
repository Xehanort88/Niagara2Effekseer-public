"""Run every tool test suite with its own arguments and print one line per suite.

    python tools/run_all_tests.py --work out/tests/all
    python tools/run_all_tests.py --work out/tests/all --only cascade,edit
    python tools/run_all_tests.py --list

Each suite runs in <work>/<suite> with its full output in <work>/logs/<suite>.log; <work>/summary.json holds
every line. Statuses:
    PASS     exit 0, a passing summary line and no skipped test
    SKIP     exit 0 and no failure, but the suite skipped tests (reasons shown); AGENTS.md counts a SKIP from the
             cascade tests as a failure to fix
    FAIL     non-zero exit, or a failure line in the output despite exit 0
    TIMEOUT  stopped after --timeout seconds (only the process tree this runner started is stopped)
    UNKNOWN  exit 0 but no summary line this runner recognises: read the log
    NOT RUN  an input the suite needs is missing (--master, --project, a font, a Python module, node)
Exit: 0 every selected suite passed; 1 any FAIL, TIMEOUT or UNKNOWN; 2 nothing failed but some suites skipped
tests or did not run, or a test file on disk is not registered here (it was not run).
"""
import argparse
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = {
    '1.70e': ROOT / 'src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll',
    '1.8': ROOT / 'src/Niagara2Efk1803/bin/Release/net9.0/Niagara2Efk.dll',
}
DEFAULT_FONT = Path('C:/Windows/Fonts/arial.ttf')                  # test_make_atlases.py's own default
FALLBACK_FONTS = ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',)  # used when Arial is absent (Linux)
DEFAULT_TS_DOSSIERS = ROOT / 'out/edits/ts-prep-20261015/census-sh/dossiers'  # test_tsport.py's own default
DEFAULT_CHROME = Path('C:/Program Files/Google/Chrome/Application/chrome.exe')  # tools/ingame/run.js's own default
TRACE_HOOK = ROOT / 'tools/testdata/trace'                     # its sitecustomize.py records reads (--trace-files)


@dataclass
class Suite:
    name: str
    script: str                       # repository-relative
    work: bool = True                 # takes --work <folder>
    modules: tuple = ()               # Python modules it imports beyond the usual numpy/Pillow
    extra: object = None              # callable(args) -> (argv list, reason it cannot run or None)
    runner: str = 'python'            # 'python' or 'node'


def needs_option(option, what, *passed):
    """The suite cannot run without --<option>; `passed` are further optional runner options passed through."""
    def extra(args):
        value = getattr(args, option.replace('-', '_'))
        if value is None:
            return [], 'needs --%s <%s>' % (option, what)
        argv = ['--%s' % option, str(value)]
        for opt in passed:
            v = getattr(args, opt.replace('-', '_'))
            if v is not None:
                argv += ['--%s' % opt, str(v)]
        return argv, None
    return extra


def font_extra(args):
    # Any complete font serves: the suite compares the committed generator with the current one on the same font.
    candidates = [Path(args.font)] if args.font else [DEFAULT_FONT] + [Path(f) for f in FALLBACK_FONTS]
    font = next((f for f in candidates if f.is_file()), candidates[0])
    if not font.is_file():
        return [], 'needs --font <a TTF/OTF>: %s not found' % font
    return ['--font', str(font)], None


def tsport_extra(args):
    folder = Path(args.tsport_dossiers) if args.tsport_dossiers else DEFAULT_TS_DOSSIERS
    if not folder.is_dir():
        return [], 'needs --tsport-dossiers <TS dossier folder>: %s not found' % folder
    return ['--dossiers', str(folder)], None


def chrome_found():
    """Whether tools/ingame/run.js finds a browser: N2E_CHROME, the Windows install, or Playwright's Chromium."""
    if (os.environ.get('N2E_CHROME') and Path(os.environ['N2E_CHROME']).is_file()) or DEFAULT_CHROME.is_file():
        return True
    node = shutil.which('node')
    probe = "try{process.stdout.write(require('playwright').chromium.executablePath())}catch(e){}"
    found = subprocess.run([node, '-e', probe], capture_output=True, text=True).stdout if node else ''
    return bool(found) and Path(found).is_file()


def ingame_extra(args):
    argv = ['--port', str(args.port)]
    if not chrome_found():                           # test_ingame.js gives run.js no --chrome: its cases cannot run
        argv.append('--no-chrome')
    if args.project:
        argv += ['--project', str(args.project)]
    return argv, None


def hdr_overlay_extra(args):
    argv, reason = needs_option('project', 'game project folder')(args)
    return argv + ['--port', str(args.port + 1)], reason


SUITES = [
    Suite('edit', 'tests/edit/run_tests.py'),
    Suite('assetinspect', 'tools/assetinspect/test_assetinspect.py'),
    Suite('author-efkauthor', 'tools/author/test_efkauthor.py'),
    Suite('author-efkbolts', 'tools/author/test_efkbolts.py'),
    Suite('cascade-bd2', 'tools/cascade/test_bd2.py'),
    Suite('cascade-build_effekseer', 'tools/cascade/test_build_effekseer.py'),
    Suite('cascade-capture', 'tools/cascade/test_capture.py'),
    Suite('cascade-cascade', 'tools/cascade/test_cascade.py'),
    Suite('cascade-digest_source', 'tools/cascade/test_digest_source.py'),
    Suite('cascade-ot2port', 'tools/cascade/test_ot2port.py'),
    Suite('cascade-sqex', 'tools/cascade/test_sqex.py', work=False),
    Suite('cascade-ue418', 'tools/cascade/test_ue418.py', work=False),
    Suite('cascade-ue423material', 'tools/cascade/test_ue423material.py', work=False,
          extra=needs_option('master', 'UE 4.23 master material export')),
    Suite('cascade-ue423shaders', 'tools/cascade/test_ue423shaders.py', work=False,
          extra=needs_option('master', 'UE 4.23 master material export', 'base')),
    Suite('cascade-tsport', 'tools/cascade/tsport/test_tsport.py', extra=tsport_extra),
    Suite('effectjob', 'tools/effectjob/test_effectjob.py'),
    Suite('fontatlas', 'tools/fontatlas/test_make_atlases.py', modules=('fontTools',), extra=font_extra),
    Suite('gradientmap', 'tools/gradientmap/test_gradientmap.py'),
    Suite('hlslcheck', 'tools/hlslcheck/test_hlslcheck.py'),
    Suite('ingame', 'tools/ingame/test_ingame.js', runner='node', extra=ingame_extra),
    Suite('ingame-hdr_overlay', 'tools/ingame/test_hdr_overlay.py', extra=hdr_overlay_extra),
    Suite('ingame-hdr_overlay_unit', 'tools/ingame/test_hdr_overlay_unit.js', work=False, runner='node'),
    Suite('ingame-lightning_audit', 'tools/ingame/test_lightning_audit.py', work=False),
    Suite('ingame-preview_backdrop', 'tools/ingame/test_preview_backdrop.py', work=False, modules=('numpy',)),
    Suite('install', 'tools/install/test_install.py'),
    Suite('iostore', 'tools/iostore/test_iostore.py'),
    Suite('meshimport', 'tools/meshimport/test_meshimport.py'),
    Suite('niagara', 'tools/niagara/test_niagara.py', work=False),
    Suite('portable', 'tools/test_portable.py'),
    Suite('rdcdigest', 'tools/rdcdigest/test_rdcdigest.py'),
    Suite('unity', 'tools/unity/test_unity.py', modules=('yaml',)),
    Suite('unity-rdc', 'tools/unity/test_unity_rdc.py'),
]

# The summary formats the suites print, anchored at the start of a line: test descriptions quote tool output such as
# "(128 failed edit read-back)". A suite's own counted summary decides; per-test FAIL lines count only for suites that
# print no summary (niagara). A status word followed by a path is a tool's per-file output echoed by a test
# (hlslcheck prints "FAIL <file>" and "SKIP <file>" for the materials test_hlslcheck makes fail on purpose), not a
# test result.
NOT_A_PATH = r'(?![ \t]+(?:[A-Za-z]:[\\/]|/|\\\\))'     # X:\ or X:/, /, and UNC \\server or \\?\
SUMMARY_FAILED = r'^(?:RESULT:\s*)?\d+ passed, (\d+) failed'    # edit, author, install, ingame, lightning_audit
SUMMARY_FAIL = [
    (re.compile(r'^(\d+) failure\(s\)', re.M), True),   # cascade, effectjob, rdcdigest, unity_rdc
    (re.compile(SUMMARY_FAILED, re.M), True),
    (re.compile(r'"(?:failures|errors)":\s*(\d+)'), True),  # the JSON summaries
    (re.compile(r'"passed":\s*false'), False),
    (re.compile(r'^FAILED\b', re.M), False),             # unittest
]
LINE_FAIL = re.compile(r'^FAIL\b' + NOT_A_PATH, re.M)   # per-test lines (niagara)
PASS_PATTERNS = [re.compile(p, re.M) for p in (
    r'^0 failure\(s\)', r'^(?:RESULT:\s*)?\d+ passed, 0 failed', r'"passed":\s*true', r'^OK\b',
    r'^PASS\b', r'\b\d+ cases passed$')]
SUMMARY = [p for p, _ in SUMMARY_FAIL] + [PASS_PATTERNS[i] for i in (0, 1, 2, 3, 5)]   # a counted verdict line
SKIP_LINE = re.compile(r'^(?:SKIP\b:?|skipped:)' + NOT_A_PATH + r'[ \t]*(.*)$', re.M)   # "SKIP ...", "skipped:"
SKIP_COUNT = [re.compile(r'skipped=(\d+)'), re.compile(r'"skipped":\s*(\d+)')]  # unittest, the JSON summaries
SKIP_NOTE = re.compile(r'\((run\.js cases skipped[^)]*)\)')          # test_ingame.js --no-chrome


def line_at(text, pos):
    """The whole output line holding position `pos`."""
    end = text.find('\n', pos)
    return text[text.rfind('\n', 0, pos) + 1:len(text) if end < 0 else end].strip()


def classify(code, text, timed_out):
    """(status, detail) for one finished suite."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    last = lines[-1] if lines else '(no output)'
    if timed_out:
        return 'TIMEOUT', last
    for pattern, counted in SUMMARY_FAIL:
        for m in pattern.finditer(text):
            if not counted or int(m.group(1)) > 0:
                return 'FAIL', line_at(text, m.start()) if code == 0 else 'exit %d: %s' % (code, last)
    if not any(p.search(text) for p in SUMMARY):
        m = LINE_FAIL.search(text)
        if m:
            return 'FAIL', line_at(text, m.start()) if code == 0 else 'exit %d: %s' % (code, last)
    if code != 0:
        return 'FAIL', 'exit %d: %s' % (code, last)
    reasons = [m.group(1).strip() or m.group(0).strip() for m in SKIP_LINE.finditer(text)]
    reasons += [m.group(1) for m in SKIP_NOTE.finditer(text)]
    counts = [int(m.group(1)) for p in SKIP_COUNT for m in p.finditer(text)]
    skipped = max([len(reasons)] + counts)            # unittest's "skipped=1" and the JSON "skipped": 1 are one test
    if skipped:
        return 'SKIP', '%d skipped: %s' % (skipped, '; '.join(reasons[:3]) or last)
    if any(p.search(text) for p in PASS_PATTERNS):
        return 'PASS', last
    return 'UNKNOWN', 'no summary line recognised; last line: %s' % last


def unregistered():
    """Test files on disk that no Suite runs, so a new suite cannot be silently left out."""
    known = {s.script for s in SUITES}
    found = set()
    for pattern in ('tools/**/test_*.py', 'tools/**/test_*.js', 'tests/**/run_tests.py'):
        for path in ROOT.glob(pattern):
            if 'node_modules' not in path.parts:
                found.add(path.relative_to(ROOT).as_posix())
    return sorted(found - known)


def stop_tree(proc):
    """Stop the suite process and everything it started (dotnet, Chrome), and nothing else."""
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)], capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except OSError:                                   # already gone
        pass
    proc.wait()


def trace_env(suite, work):
    """Environment that makes every Python process of the suite record the external files it reads
    (tools/testdata/trace/sitecustomize.py), and the trace file it writes to."""
    traces = work / 'traces'
    traces.mkdir(parents=True, exist_ok=True)
    trace = traces / ('%s.tsv' % suite.name)
    trace.write_text('', encoding='utf-8')
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(p for p in (str(TRACE_HOOK), env.get('PYTHONPATH')) if p)
    env['N2E_TRACE_FILE'] = str(trace)
    env['N2E_TRACE_IGNORE'] = os.pathsep.join([str(work), tempfile.gettempdir()])   # generated, never external
    env['N2E_TRACE_REPO'] = str(ROOT)                          # repository files are not external data...
    env['N2E_TRACE_KEEP'] = str(ROOT / 'out')                  # ...except under out/ (catalogue, other tasks' outputs)
    return env, str(trace)


def run_suite(suite, args, work, logs):
    """Run one suite; returns its summary.json row."""
    row = {'suite': suite.name, 'script': suite.script}
    if not (ROOT / suite.script).is_file():          # the public repository leaves some tool folders out
        row.update(status='NOT RUN', detail='%s is not in this checkout' % suite.script)
        return row
    reasons = ['needs Python module %s' % m for m in suite.modules if importlib.util.find_spec(m) is None]
    exe = sys.executable if suite.runner == 'python' else shutil.which('node')
    if exe is None:
        reasons.append('needs node on PATH')
    extra, reason = suite.extra(args) if suite.extra else ([], None)
    if reason:
        reasons.append(reason)
    if reasons:
        row.update(status='NOT RUN', detail='; '.join(reasons))
        return row
    argv = [exe, str(ROOT / suite.script)]
    if suite.work:
        folder = work / suite.name
        folder.mkdir(parents=True, exist_ok=True)
        argv += ['--work', str(folder)]
    argv += extra
    log = logs / ('%s.log' % suite.name)
    row.update(command=argv, log=str(log))
    env = None
    if args.trace_files:
        env, row['trace'] = trace_env(suite, work)
    group = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    start = time.time()
    with open(log, 'wb') as out:
        proc = subprocess.Popen(argv, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=env, **group)
        timed_out = False
        try:
            proc.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            stop_tree(proc)
    status, detail = classify(proc.returncode, log.read_text(encoding='utf-8', errors='replace'), timed_out)
    row.update(status=status, detail=detail, exit=proc.returncode, seconds=round(time.time() - start, 1))
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--work', type=Path, default=ROOT / 'out/tests/all', help='default out/tests/all')
    ap.add_argument('--only', help='comma-separated parts of suite names, e.g. cascade,edit')
    ap.add_argument('--list', action='store_true', help='list the suites and exit')
    ap.add_argument('--timeout', type=float, default=1200, help='seconds per suite (default 1200)')
    ap.add_argument('--port', type=int, default=8931,
                    help='port of test_ingame.js; test_hdr_overlay.py gets the next one (default 8931)')
    ap.add_argument('--master', type=Path, help='passed to the test_ue423* suites')
    ap.add_argument('--base', type=Path, help='passed to test_ue423shaders.py')
    ap.add_argument('--project', type=Path, help='game project: test_hdr_overlay.py, live cases of test_ingame.js')
    ap.add_argument('--font', type=Path, help='font for test_make_atlases.py (default %s, else %s)'
                    % (DEFAULT_FONT, ', '.join(FALLBACK_FONTS)))
    ap.add_argument('--tsport-dossiers', type=Path, help='TS dossier folder for test_tsport.py')
    ap.add_argument('--trace-files', action='store_true',
                    help='record the external files each Python suite reads into <work>/traces/<suite>.tsv '
                         '(tools/testdata/pack.py turns them into an upload folder)')
    args = ap.parse_args()

    stray = unregistered()
    selected = SUITES
    if args.only:
        parts = [p.strip() for p in args.only.split(',') if p.strip()]
        selected = [s for s in SUITES if any(p in s.name for p in parts)]
        if not selected:
            sys.exit('--only %s matches no suite (--list shows them)' % args.only)
    if args.list:
        for s in selected:
            print('%-26s %s%s' % (s.name, s.script, '' if s.work else '  (no --work)'))
        for path in stray:
            print('NOT REGISTERED             %s' % path)
        return 0

    work = args.work.resolve()
    logs = work / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    for build, dll in TOOLS.items():
        print('tool %-5s %s %s' % (build, 'built  ' if dll.is_file() else 'MISSING', dll.relative_to(ROOT)))
    for path in stray:
        print('WARNING: %s is not registered in tools/run_all_tests.py and was not run' % path)
    print('work: %s' % work, flush=True)

    rows = []
    for suite in selected:
        if sys.stdout.isatty():                       # progress only on a terminal, not in a piped log
            print('... %s' % suite.name, end='\r', flush=True)
        row = run_suite(suite, args, work, logs)
        rows.append(row)
        print('%-8s %-26s %s' % (row['status'], suite.name, row['detail'][:160]), flush=True)

    counts = {}
    for row in rows:
        counts[row['status']] = counts.get(row['status'], 0) + 1
    order = ('PASS', 'SKIP', 'NOT RUN', 'FAIL', 'TIMEOUT', 'UNKNOWN')
    print('RESULT: ' + ', '.join('%d %s' % (counts[s], s) for s in order if s in counts)
          + ('; %d test file(s) not registered' % len(stray) if stray else ''))
    (work / 'summary.json').write_text(json.dumps({'suites': rows, 'unregistered': stray}, indent=1), encoding='utf-8')
    if any(counts.get(s) for s in ('FAIL', 'TIMEOUT', 'UNKNOWN')):
        return 1
    return 2 if counts.get('SKIP') or counts.get('NOT RUN') or stray else 0


if __name__ == '__main__':
    sys.exit(main())
